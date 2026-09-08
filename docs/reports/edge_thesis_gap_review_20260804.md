# 엣지 thesis 보완 검토·시뮬레이션 리포트 (2026-08-04)

대상: `docs/reports/edge_thesis_20260804.md`

## 결론

엣지의 방향은 타당하다. 다만 현재 시스템은 **“정보 없는 강제 매도”를 선별하고, 같은 계약으로 실제 체결·정산한다**는 thesis를 완전히 구현한 상태가 아니다.

현재 가장 강한 자산은 새 예측모델보다 다음 검증 장치다.

- US day_losers + GBM 랭킹, KR R2 후보 정의, TP12/SL25/D5 계약을 각각 분리해 검증한다.
- point-in-time·forward·cost·authority를 분리하고, 기준 미달이면 fail-closed 한다.
- shadow·counterfactual·사전등록 기준이 있어 결과에 따라 골대를 옮기기 어렵다.

반면 당장 보완해야 할 핵심은 네 가지다.

1. **정보성 하락 배제가 코드에서 enforce되지 않는다.** `us_swing_shadow_runner.py`는 `news_or_earnings_flag`를 저장하지만 후보 제외 조건으로 사용하지 않는다. earnings 차단은 PathB에만 있고 US swing handoff에는 없다.
2. **US swing 계약의 검증 금액이 문서와 다르다.** 운영 문서는 30만원을 말하지만 현재 execution shadow는 `500,000 × micro 0.1 = 50,000원`이다. 30만원 전략의 forward 표본으로 볼 수 없다.
3. **실행 가능한 결과의 표본이 아직 음수다.** 현재 US execution shadow는 3건 중 2건 정산, 평균 `-1.80%`, PF `0.76`; 확대나 레인 우월성의 근거가 아니다.
4. **KR R2는 전략이 준비됐을 뿐 파이프라인·forward가 연결됐다고 볼 수 없다.** 최근 원장 13행 중 R2 통과 0행이고, 3,000건 pipeline replay에서 KR actionable은 0건이었다.

따라서 현재 상태의 정확한 표현은 **“검증된 후보 정의를 가진 두 레인의 fail-closed shadow/소액 실험 단계”**다. “US rank1 실매수 일1건이 돌아간다”는 운영 문서의 표현은 현재 status 원장과 맞지 않는다.

## 1. 현재 상태 대조

| 항목 | 문서상 표현 | 현재 원장·코드 | 판정 |
|---|---|---|---|
| US authority | micro, rank1 실매수 | `state/us_swing_status.json`: `effective_mode=shadow`, `allowed_to_emit_orders=false` | 문서 과장/상태 불일치 |
| US forward | 1/30 진행 | execution shadow `matured=2`, `sessions=2`, 평균 `-1.80%`, PF `0.76` | 확대 근거 없음 |
| US budget | 주문 30만원 | shadow budget `50,000원`, config cap `300,000원` | 계약 불일치 |
| US breadth | loader 존재 | 최근 status `breadth_context_state=MISSING` | 저장만 되고 활용 불가 |
| KR R2 | 9월 초 합류 준비 | 최근 shadow 13행, R2 pass 0; actionable 0 | 아직 연결·검증 전 |
| 현재 보유/실행 symbol | FRMI 관리 | swing shadow active row는 NVTS | broker·shadow·운영 문서 귀속 재조정 필요 |

`state/us_swing_execution_status.json`에는 과거 `historical_policy_hash_mismatch`가 남아 있고, `state/us_swing_status.json`에는 해당 blocker가 없다. 현재 config hash 자체는 historical evidence와 일치한다. 즉 전략 결과뿐 아니라 **status artifact 생성 시점도 동기화되지 않았다.**

## 2. 시스템이 잘할 수 있는데 놓친 것

### 2.1 뉴스·실적 데이터가 이미 있는데 급락 레인에서 쓰지 않는다

`data/earnings_calendar.json`은 2026-08-04 기준 US 1,496종목·KR 21종목을 보유하고, preopen 후보에는 `news_or_earnings_flag`, `news_signal_type`이 있다. 그러나 US swing 후보 적격 조건에는 이 값이 없다.

최근 rank1 선출 이력 15행을 후보 snapshot과 대조하면 7행이 news/earnings flag=true였다. 2026-07-29에는 실제 rank1 `NVTS`가 `weak_generic` flag=true였고, 같은 후보군에서 flag를 제외한 counterfactual rank1은 `FRMI`로 바뀐다. 2026-08-03에도 rank1 `FRMI`는 유지되지만 rank2 `GDDY`와 일부 후보가 제거된다.

이 결과는 필터가 수익을 높인다는 증거가 아니다. 다만 thesis의 “정보성 하락은 안 산다”가 현재 코드에서 보장되지 않는다는 직접 증거다. **정보 flag가 unknown인 경우까지 `no-news`로 취급하지 않는 fail-closed 규칙**이 필요하다.

### 2.2 시장·업종 상대수익 데이터가 준비됐지만 랭킹에 연결되지 않는다

US runner는 SPY/QQQ/IWM 계열 benchmark와 breadth loader를 갖고 있지만, 최근 breadth 상태는 `MISSING`이다. sector map 파일도 존재하지만 `sector_map.json.disabled_until_restart` 상태이며 KR 매핑은 비어 있다. 현재 GBM 입력에는 종목·시장 일봉 특성이 중심이고, breadth/sector-neutral excess return이 US swing ranking의 enforce 입력이 아니다.

따라서 “정보 없는 종목 급락”과 “시장/업종 전체 급락”을 구분하는 시스템 능력을 실제 선별에 쓰지 못한다. 단, 이 데이터를 곧바로 점수에 더할 것이 아니라 다음 nested ablation으로 검증해야 한다.

```text
B0 현행 GBM
B1 B0 + breadth/risk regime
B2 B1 + sector relative return
B3 B2 + 실제 spread/quote age
```

### 2.3 profit-path 모델은 있으나 아직 승격 조건을 만들지 못했다

`state/models/profit_path_US.json`은 validation AUC `0.638`, calibration ECE `0.0096`, drift `healthy`를 기록한다. 그러나 `validation_selected_n=0`, `promotion_eligible_backtest=false`다. 모델을 live authority에 넣지 않은 것은 맞지만, 후보 전체 shadow scoring·coverage·selected cohort별 net을 별도 축으로 쌓으면 GBM과의 역할 분담을 검증할 수 있다.

### 2.4 tail-capture는 PathB에만 연결돼 swing 레인의 상방 꼬리를 검증하지 않는다

`runtime/tail_capture.py`는 MFE 4% 이후 trailing/carry 결정을 만들고 `runtime/pathb_runtime.py`에 연결돼 있다. 하지만 `us_swing_5d`는 별도 fixed TP/D5 risk contract로 관리된다. thesis가 “TP12는 상한이 아니라 하한”이라고 주장하는 만큼, US swing에 tail-capture를 섞어서는 안 되지만 **동일 원장에 별도 counterfactual로 검증할 가치**가 있다.

## 3. 시뮬레이션 결과

### 3.1 최신 pipeline replay

실행: `tools/pipeline_simulation_matrix.py --since 2026-07-25 --market both --limit 3000`

| 시장 | 후보 | prompt | actionable | safety submit | counterfactual 관찰 |
|---|---:|---:|---:|---:|---|
| US | 2,000 | 863 | 4 | 4 | `opening_range_break` 보강 시 28건, 3개 확인 필드 보강 시 29건 route 회복 |
| KR | 1,000 | 827 | 0 | 0 | `opening_range_break` 보강 시 42건 route 회복, 그러나 수익성·실제 주문 가능성은 미검증 |

재생 fidelity는 evidence state mismatch 3건, route mismatch 1건이었다. 중요한 것은 US에서 실행 가능 4건 중 candidate audit fill이 0건이라는 점이다. canonical fill이 있어도 후보 row와 연결되지 않으면 학습·성과 귀속에 쓸 수 없다.

### 3.2 결손 피처 주입 결과

US 최근 replay의 결손 상위는 `volume_ratio_open` 328, `vwap_distance_pct` 327, `opening_range_break` 282, `ret_3m/ret_5m` 각 153건이다. `time_normalized_rvol`은 120건에만 존재하고 route 회복은 1건이었다.

이는 “전략이 후보를 거부했다”와 “전략 입력이 완성되지 않았다”를 분리해야 한다는 뜻이다. 특히 `opening_range_break` 보강만으로 28건이 회복되므로, 현재 시스템은 이미 가지고 있는 장중 확인값을 최종 signal/route까지 일관되게 전달하지 못하는 사례가 남아 있다.

### 3.3 US historical contract와 현재 forward의 분리

기존 sealed OOS exit simulation은 293세션·879행에서 TP12/SL25/D5, 비용 0.5% 기준 평균 `+0.72%`, PF `1.30`이지만 block LCB는 `-0.375%`다. rank1 skip capacity simulation은 55건, 평균 `+2.09%`, PF `1.51`이지만 일봉 open proxy와 historical fill 가정이다.

반면 현재 실행 shadow는 3건 중 2건 정산, 평균 `-1.80%`, PF `0.76`이다. 결론은 historical simulation이 틀렸다는 것이 아니라 **현재 forward에서 아직 재현되지 않았다**는 것이다. 두 수치를 한 평균으로 합치면 안 된다.

### 3.4 정보성 하락 제외 counterfactual

최근 후보군에서 `news_or_earnings_flag=true`를 scoring 전에 제거해 보았다.

| signal date | 재생 current scorer rank1 | flag | flag 제외 rank1 | 결과 정산 |
|---|---|---:|---|---|
| 2026-07-29 | NVTS | true | FRMI | 아직 D5 미성숙 |
| 2026-07-30 | INIO | false | INIO | 아직 D5 미성숙 |
| 2026-07-31 | GPI | false | GPI | 아직 D5 미성숙 |
| 2026-08-03 | FRMI | false | FRMI | 아직 D5 미성숙 |

이 표는 현재 저장된 DB rank를 복원한 것이 아니라, 같은 현재 scorer를 후보 snapshot에 다시 적용한 counterfactual이다. 따라서 필터 승격 근거가 아니라, **필터가 실제 rank를 바꾸는지 검증 가능한 상태**라는 뜻이다. 최소 30세션의 paired shadow와 `TARGET/STOP/NO_TOUCH`, 시장·업종 초과수익, 실제 net 비용을 쌓은 뒤에만 판정한다.

## 4. 아직 해보지 않은 것

우선순위 순이다.

1. **Information-free ablation**: news/earnings/disclosure flag를 `exclude`, `separate lane`, `unknown abstain` 세 arm으로 분리하고 동일 모델·동일 rank·동일 계약으로 비교.
2. **Breadth/sector-neutral ablation**: breadth가 실제로 채워진 기간을 별도 holdout으로 만들고 raw net과 market/sector excess net을 비교.
3. **실제 quote 비용 검증**: bid/ask, spread bps, quote age, fill latency, FX spread를 저장해 고정 0.5% 대신 실현 net으로 promotion.
4. **30만원 capacity 검증**: 5만원 shadow와 30만원 live cap을 동일 후보·동일 시각으로 paired replay. 한 주 매수 불가·유동성 하한·슬리피지 cap을 각각 분해.
5. **rank1/3 및 slot 정책 비교**: rank1 only, rank1~3, score-weighted sizing을 세션 단위 block bootstrap으로 비교. 단, 30건 기준 통과 전 live 확대는 금지.
6. **KR R2 end-to-end rehearsal**: 실제 R2 pass row가 없는 현재는 synthetic fixture로 next-open→TP/SL/D5→broker truth→fill attribution 전부를 한 번 재생하고, 이후 첫 실제 R2 pass를 같은 계약으로 처리.
7. **상방 꼬리 counterfactual**: US swing의 TP12 즉시 청산과 tail-capture wide trail/carry를 별도 shadow로 비교. 실행 소유권을 한 번에 바꾸지 않는다.
8. **포트폴리오 상관·동시손실**: 동일 업종/시장 급락, US-KR 동시 risk-off, 3슬롯 동시 SL을 block simulation하고 aggregate drawdown을 산출.
9. **귀속 완결성**: candidate audit fill 0건 문제를 고쳐 `candidate → decision → order → fill → close → net` coverage 100%를 만들기 전 학습·승격에 사용하지 않음.

## 5. 보완 순서

### P0 — thesis와 실제 거래 계약을 일치

- US swing에 `news_or_earnings_flag`, earnings window, direct disclosure, unknown-data 정책을 연결한다.
- shadow budget·authority cap·실주문 cap을 단일 계약 파일에서 읽게 한다. 현재 5만원 shadow로 30만원 계약을 검증하는 구조를 제거한다.
- `effective_config_hash`, `status_generated_at`, `authority_effective_mode`, `broker_source_strategy`를 한 status snapshot에 저장한다.
- 운영 문서의 “실매수” 표현은 `allowed_to_emit_orders=true`와 broker fill이 확인될 때만 사용한다.

### P1 — 데이터가 실제 판단까지 도달하도록 연결

- `opening_range_break`, RVOL, VWAP, ret_3m/5m의 generated/consumed/persisted 카운터를 추가한다.
- breadth/sector map을 먼저 복구하고, 결측이면 점수 0으로 조용히 대체하지 말고 `missing_reason`을 남긴다.
- candidate audit에 lifecycle `FILLED`를 직접 연결하고, route/action mismatch·WATCH row 오염을 reject한다.

### P2 — 수익성 개선 실험

- 정보성 하락 제외, sector excess, true spread, profit-path evidence, tail-capture를 한 번에 하나씩 shadow한다.
- 모든 비교는 session block 단위, 동일 후보군·동일 비용·동일 entry/exit contract로 한다.
- forward 기준은 net 합, benchmark/sector alpha, PF, block LCB, tail concentration, worst streak를 함께 본다.

## 6. 최종 판정

- **유지할 것**: 급락 레인 분리, GBM/rank 구조, KR R2 후보 정의, TP12/SL25/D5의 bounded contract, 사전등록·fail-closed 검증기.
- **즉시 보완할 것**: 정보성 이벤트 배제, 5만원/30만원 계약 정합성, status·broker·shadow 귀속 통합, 장중 피처 배선, fill attribution.
- **아직 주장하면 안 되는 것**: US 레인의 실전 우위 확정, 30만원 capacity, KR R2 live 준비 완료, 뉴스 없는 급락만 선별한다는 운영 보장.

다음 의사결정은 새 전략을 추가하는 것이 아니라, **동일 후보를 `정보 있음/없음`, `5만원/30만원`, `raw/sector excess`, `fixed TP/tail counterfactual`로 나눠 paired shadow하는 것**이다. 그 결과가 양수 net과 양수 alpha를 동시에 보일 때만 현재 thesis의 다음 단계로 간다.

검증 실행 결과:

- `python tools/kr_fallen_gate_report.py`: 최근 13행·3세션, R1/R2/R3 통과·정산 0.
- `python tools/pipeline_simulation_matrix.py --since 2026-07-25 --market both --limit 3000`: evidence mismatch 3, route mismatch 1.
- 관련 테스트: `52 passed` (KR bridge/config), `63 passed` (US swing authority/runner/handoff/bridge/tail/profit path/rehearsal 관련).
- 관련 모듈 compileall: PASS.
