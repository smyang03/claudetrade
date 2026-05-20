# Later Work Plan - 2026-05-20

KR PathB shadow registration/code-level plan: [kr_pathb_shadow_registration_plan_20260520.md](kr_pathb_shadow_registration_plan_20260520.md)

이 문서는 2026-05-20 기준 나중에 해야 할 항목만 분리한 플랜이다. 기존 `docs/TODO_ROADMAP.md`와 `docs/DEVELOPED_WORK.md`는 수정하지 않고, 즉시 범위 밖의 작업을 단계별로 정리한다.

## 시작 조건

나중 플랜은 다음 조건이 충족된 뒤 순차적으로 착수한다.

- P0-1 live truth gate 결과가 안정적으로 저장된다.
- P0-2 KIS fill truth 실수신 검증에서 full/partial/cancel과 재기동 cache 복원이 확인된다.
- P0-3~P0-5로 canonical performance, decisions fill link, counterfactual outcome 기준이 생긴다.

## Phase L0 - 남은 P0 운영 검증

| 항목 | 작업 | 효과 | 착수 조건 | 완료 조건 |
| --- | --- | --- | --- | --- |
| P0-6 | metric contract 고정 | 분석 리포트의 분모와 bucket 혼합으로 정책 판단이 뒤집히는 일을 줄인다. | P0-3~P0-5 산출물 설계 시점 | 모든 분석 리포트가 live/paper, raw/dedupe, latest/state, raw/pure watch 축과 bucket 분리를 명시 |
| P0-7 | final prompt evidence alignment 운영 검증 | 실제 세션에서 prompt 후보와 실행 후보가 같은 pool인지 확인한다. | 다음 live/session 데이터 확보 | `evidence_prompt_overlap_ratio >= 0.8`, `prompt_exec_missing_pct` 감소, READY 0 반복 여부 확인 |
| P0-8 | prompt overlay shadow gate 관찰 | prompt 변경의 성과 개선 여부를 live 전환 전에 검증한다. | shadow 데이터 누적 시작 | 10거래일 이상, overlay 발동 4일 이상, PF > 1.0, top-day 기여율 < 40%, `overlay_plan_b_used=false` |

## Phase L1 - 안전장치와 실행 품질

| 항목 | 작업 | 효과 | 완료 조건 |
| --- | --- | --- | --- |
| P1-1 | KR modern action schema/route 점검 | BUY 가능한 신호와 wait/watch 신호 혼동을 줄인다. | `BUY_READY`, `PROBE_READY`, `PULLBACK_WAIT`, `ADD_READY`를 route/fill 기준으로 분리해 case table 작성 후 gate 수정 여부 결정 |
| P1-2 | KR entry timing gate 재검토 | 손실이 반복되는 시간대를 진입 정책에 반영한다. | canonical table 기준 시간대별 정책 확정 |
| P1-3 | `RiskManager` KR/US adapter live 이행 | 한 시장의 cash/position/halt 상태가 다른 시장 주문을 오염시키는 위험을 낮춘다. | `_risk(market)` 호출부 확대, adapter live 전환 테스트, 시장별 회귀 추가 |
| P1-4 | Safety/equity audit field 확장 | safety 판단 근거의 source/lag/unrealized 분해가 가능해진다. | `equity_source`, `unrealized_return_pct`, `broker_lag_suspected` 등 저장 |
| P1-5 | slot-disabled momentum shadow label | live 금지 슬롯의 기회비용을 표본으로 판단한다. | `slot_disabled:momentum` metadata 저장, 10 labeled sessions 전 live 승격 금지 |
| P1-6 | KIS microstructure/MFE 보강 | KR 호가/VI/MFE 분석 신뢰도를 높인다. | source/sample audit, minute cache 기반 MFE 재계산, degraded 시 주문 경로 영향 없음 확인 |
| P1-7 | Claude API 비용 절감 1차 | 호출 비용 절감과 품질 저하 여부를 분리 관찰한다. | AUTO_SELL_REVIEW를 단일 Claude 호출로 통합(escalation 경로 보존), hold cache, preopen duplicate cache를 shadow 지표와 함께 적용 |
| P1-8 | Intraday recheck 손상 상태 하드닝 | pending recheck 레코드가 `due_at`, `pending_at`, `session`까지 손상된 경우에도 무기한 pending이 남지 않게 한다. | pending인데 `pending_intraday_recheck_at/session` 누락 시 warning + stale/expire 정책 확정, 다음 세션 expire 또는 보수적 수동알림 테스트 추가 |
| P1-9 | Intraday recheck TRAIL verdict 분리 | recheck pending 상태에서 SELL이 아닌 `TRAIL` 응답을 HOLD와 구분해 기록/표시 의미를 더 정확히 한다. | `trail_after_recheck` 또는 `non_sell_after_recheck` verdict 설계, pending clear 기준 유지, Telegram/log 표시 회귀 테스트 |

## Phase L2 - Shadow 실험과 후보 품질

| 항목 | 작업 | 효과 | 완료 조건 |
| --- | --- | --- | --- |
| P2-1 | US preopen/extended-hours 관찰 | preopen 손실이 구조 문제인지 표본 착시인지 구분한다. | preopen 신규 진입 임시 보수 가드 유지, 10 decision 이상 후 재평가 |
| P2-2 | Hybrid-lite/WATCH_TRIGGER shadow 고도화 | missed runup과 watch-only 기회 라벨 품질을 높인다. | miss-quality, one-block relaxed, watch-only missed runup을 pure bucket 기준으로 평가 |
| P2-3 | PathB EXPIRED 재샘플링과 sell pending remainder | 놓친 진입과 미정리 잔량 리스크를 줄인다. | EXPIRED 전 quote refresh/zone reentry shadow, sell partial remainder 재주문 테스트 |
| P2-4 | CandidateTierBook shadow | 후보 상태를 CORE/WATCH/BENCH/QUARANTINE으로 분리한다. | shadow tier book과 기존 list diff 검증 후 source-of-truth 전환 판단 |
| P2-5 | KRX/BigKinds/theme injection | KR 공식/뉴스/테마 metadata로 후보 품질을 보강한다. | key 확보, dry-run, normalized SQLite, theme candidate injection shadow |
| P2-6 | Market Regime / RR check shadow | 변동성/손익비가 나쁜 setup을 observe-only로 검증한다. | 5거래일 shadow, RR threshold observe-only, 충돌 테스트 후 하나씩 live 판단 |
| P2-7 | Prompt overlay cap 확대/Shadow Claude call/Fresh slot | prompt 실험 범위를 단계적으로 키우며 비용과 과최적화 리스크를 관리한다. | later-data gate 통과 후 4->6->8 단계 확대, 필요 시 audit-only dry-run Claude call |

## Phase L3 - 구조 개편과 장기 보류

| 항목 | 작업 | 효과 | 착수 조건 |
| --- | --- | --- | --- |
| P3-1 | L3 price collection inject | 가격 이력이 반복적으로 비는 수동/핀 후보의 분석 공백을 줄인다. | 핀/수동 후보가 2세션 이상 `HISTORY_UNAVAILABLE`로 반복될 때 |
| P3-2 | Dual runtime SharedEngine/AccountRuntime | KR/US runtime 분리를 구조적으로 강화한다. | `RiskManager` KR/US live adapter와 performance truth 안정 후 |
| P3-3 | Brain Train 모드 | 정책 메모리 자동 변경 없이 별도 weight/flag 실험을 가능하게 한다. | 운영 전략 품질 안정 후에도 샘플 부족이 명확할 때 |
| P3-4 | 신규 intraday/VWAP/momentum opening gate | 신규 장중 전략을 작은 shadow 실험으로 검증한다. | P0/P1 안정화와 shadow 성과 확인 후 |

## 보류 원칙

- 짧은 기간 성과만으로 live 정책을 바로 바꾸지 않는다.
- shadow 관찰, 검증, 승인 순서 없이 신규 전략 gate를 켜지 않는다.
- `state/brain.json` 자동 변경은 이 플랜 범위에 포함하지 않는다.
- broker truth와 performance truth가 안정되기 전에는 size 확대, prompt overlay live 전환, 신규 momentum live 승격을 하지 않는다.
