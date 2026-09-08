# Pipeline Case Injection Review 2026-07-23

목적: DB의 실제 미매수 후보를 대표 유형별로 뽑아 `build_live_evidence_pack()`과 `route_candidate_action()`에 원본/주입 데이터를 직접 넣고, 미매수가 데이터 누락인지 전략상 정상 차단인지 검증한다.

## Summary

- DB: `E:\code\claudetrade\data\audit\candidate_audit.db`
- 기간: `2026-07-01` 이후
- 케이스 수: 20
- data_or_wiring_gap_recovers: 8
- normal_price_cap_block: 2
- strategy_gate_holds: 2
- submit_layer_block_after_route_open: 8

## Case Results

### KR_partial_missing_live_data / KR 000660 / cand_66c5d3c708d1ed1f1b25

- 시각: 2026-07-01 2026-07-01T09:00:30+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `WATCH` / `` / reason=`kr_early_entry_confirmation_required` / gate=`` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: clear_soft_blocks

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | partial/PROBE_READY | WATCH/None | soft_block_floor:late_mover | ret_3m_pct,ret_5m_pct,opening_range_break |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| inside_entry_cap | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| complete_pathb_plan | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |

### KR_partial_missing_live_data / KR 121440 / cand_b85f25a75689f1c03d7c

- 시각: 2026-07-01 2026-07-01T09:00:30+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `WATCH` / `` / reason=`kr_early_entry_confirmation_required` / gate=`` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: clear_soft_blocks

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | partial/PROBE_READY | WATCH/None | soft_block_floor:late_mover | ret_3m_pct,ret_5m_pct,opening_range_break |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| inside_entry_cap | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| complete_pathb_plan | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | soft_block_floor:late_mover |  |

### KR_buy_ready_price_cap_exceeded / KR 403870 / cand_ab34d2b80b82f6194d9e

- 시각: 2026-07-03 2026-07-03T09:40:31+09:00
- Claude action: `BUY_READY`
- 기록 route: `WATCH` / `` / reason=`buy_ready_price_cap_exceeded` / gate=`entry_price_cap_exceeded` / no_submit=`None`
- 분류: `normal_price_cap_block`
  - 상한 안쪽으로 넣으면 매수 경로가 열림: 가격 차단 자체는 정상

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| inside_entry_cap | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |

### KR_buy_ready_price_cap_exceeded / KR 055550 / cand_9a7e6ccba54d231cfe09

- 시각: 2026-07-03 2026-07-03T10:20:07+09:00
- Claude action: `BUY_READY`
- 기록 route: `WATCH` / `` / reason=`buy_ready_price_cap_exceeded` / gate=`entry_price_cap_exceeded` / no_submit=`None`
- 분류: `normal_price_cap_block`
  - 상한 안쪽으로 넣으면 매수 경로가 열림: 가격 차단 자체는 정상

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| inside_entry_cap | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |

### KR_confirmation_not_confirmed / KR 066980 / cand_a3f79719bd09b19337c7

- 시각: 2026-07-01 2026-07-01T09:28:24+09:00
- Claude action: `BUY_READY`
- 기록 route: `WATCH` / `` / reason=`probe_ready` / gate=`kr_early_entry_buy_demoted_to_probe` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: inside_entry_cap, complete_pathb_plan
- 필드/배선 의심:
  - claude_action=BUY_READY, runtime_gate.route_requested_action=PROBE_READY 불일치

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| inside_entry_cap | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |

### KR_confirmation_not_confirmed / KR 475150 / cand_4b91901fa0289d7ede2f

- 시각: 2026-07-01 2026-07-01T12:29:20+09:00
- Claude action: `BUY_READY`
- 기록 route: `WATCH` / `` / reason=`probe_ready` / gate=`kr_late_fresh_buy_demoted_to_probe` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: inside_entry_cap, complete_pathb_plan
- 필드/배선 의심:
  - claude_action=BUY_READY, runtime_gate.route_requested_action=PROBE_READY 불일치

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| inside_entry_cap | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | entry_price_cap_exceeded |  |

### KR_pullback_evidence_gate / KR 011230 / cand_11f9bb2d578c6852184f

- 시각: 2026-07-01 2026-07-01T09:59:09+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `WATCH` / `` / reason=`pullback_wait_evidence_gate` / gate=`pullback_wait_evidence_gate` / no_submit=`None`
- 분류: `strategy_gate_holds`
  - 명시 차단 사유 유지: pullback_wait_evidence_gate

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/WATCH | WATCH/None | negative_pullback_context |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| inside_entry_cap | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| complete_pathb_plan | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| clear_soft_blocks | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |

### KR_pullback_evidence_gate / KR 066980 / cand_9b151327c8992e3b905c

- 시각: 2026-07-01 2026-07-01T09:59:09+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `WATCH` / `` / reason=`pullback_wait_evidence_gate` / gate=`pullback_wait_evidence_gate` / no_submit=`None`
- 분류: `strategy_gate_holds`
  - 명시 차단 사유 유지: pullback_wait_evidence_gate

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/WATCH | WATCH/None | negative_pullback_context |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| inside_entry_cap | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| complete_pathb_plan | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| clear_soft_blocks | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | negative_pullback_context |  |

### KR_pathb_claude_price_invalid / KR 010140 / cand_25622360dd24e63cb9fe

- 시각: 2026-07-01 2026-07-01T09:59:09+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `PULLBACK_WAIT` / `PathB.wait` / reason=`pullback_wait` / gate=`` / no_submit=`CLAUDE_PRICE_INVALID`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 CLAUDE_PRICE_INVALID 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_data | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| inside_entry_cap | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_pathb_plan | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |

### KR_pathb_claude_price_invalid / KR 080220 / cand_320eeefbd72a48568a01

- 시각: 2026-07-01 2026-07-01T10:07:55+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `PULLBACK_WAIT` / `PathB.wait` / reason=`pullback_wait` / gate=`` / no_submit=`CLAUDE_PRICE_INVALID`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 CLAUDE_PRICE_INVALID 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_data | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| inside_entry_cap | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_pathb_plan | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |

### US_buy_ready_no_signal / US VSAT / cand_31dae30e799596524304

- 시각: 2026-07-01 2026-07-01T23:14:27+09:00
- Claude action: `BUY_READY`
- 기록 route: `BUY_READY` / `PlanA.buy` / reason=`buy_ready` / gate=`` / no_submit=`NO_SIGNAL`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 NO_SIGNAL 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_post_open_data | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_post_open_no_orb | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| inside_entry_cap | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |

### US_buy_ready_no_signal / US RDDT / cand_370b1688ab0a59ed03bd

- 시각: 2026-07-01 2026-07-02T00:41:42+09:00
- Claude action: `BUY_READY`
- 기록 route: `BUY_READY` / `PlanA.buy` / reason=`buy_ready` / gate=`` / no_submit=`NO_SIGNAL`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 NO_SIGNAL 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_post_open_data | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_post_open_no_orb | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| inside_entry_cap | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |

### US_probe_no_signal / US IREN / cand_ecfe9783b3bb34728d63

- 시각: 2026-07-02 2026-07-02T22:38:21+09:00
- Claude action: `PROBE_READY`
- 기록 route: `PROBE_READY` / `PlanA.probe` / reason=`probe_ready` / gate=`` / no_submit=`NO_SIGNAL`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 NO_SIGNAL 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | partial/PROBE_READY | PROBE_READY/PlanA.probe | probe_ready | opening_range_break |
| complete_post_open_data | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| inside_entry_cap | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |

### US_probe_no_signal / US RIVN / cand_4422f32b0c43b22cfd94

- 시각: 2026-07-06 2026-07-07T02:38:57+09:00
- Claude action: `PROBE_READY`
- 기록 route: `PROBE_READY` / `PlanA.probe` / reason=`probe_ready` / gate=`` / no_submit=`NO_SIGNAL`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 NO_SIGNAL 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| complete_post_open_data | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| inside_entry_cap | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| complete_pathb_plan | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| clear_soft_blocks | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PROBE_READY/PlanA.probe | probe_ready |  |

### US_pullback_evidence_gate / US RDDT / cand_3fa1bdb84e8bc616a458

- 시각: 2026-07-02 2026-07-02T22:38:21+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `WATCH` / `` / reason=`pullback_wait_evidence_gate` / gate=`pullback_wait_evidence_gate` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: complete_post_open_data, complete_post_open_no_orb, inside_entry_cap, complete_pathb_plan, clear_soft_blocks

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | partial/PROBE_READY | PULLBACK_WAIT/PathB.wait | pullback_wait | opening_range_break |
| complete_post_open_data | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| inside_entry_cap | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_pathb_plan | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |

### US_pullback_evidence_gate / US RDDT / cand_6361986d6d6bfdd403d6

- 시각: 2026-07-02 2026-07-02T22:38:21+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `WATCH` / `` / reason=`pullback_wait_evidence_gate` / gate=`pullback_wait_evidence_gate` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: complete_post_open_data, complete_post_open_no_orb, inside_entry_cap, complete_pathb_plan, clear_soft_blocks

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | partial/PROBE_READY | PULLBACK_WAIT/PathB.wait | pullback_wait | opening_range_break |
| complete_post_open_data | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| inside_entry_cap | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_pathb_plan | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |

### US_soft_gate_override_failed / US BE / cand_132c50492927fbef43d3

- 시각: 2026-07-06 2026-07-06T23:07:03+09:00
- Claude action: `BUY_READY`
- 기록 route: `WATCH` / `` / reason=`soft_gate_override_failed` / gate=`soft_gate_override_failed` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: clear_soft_blocks

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| inside_entry_cap | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| complete_pathb_plan | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| clear_soft_blocks | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |

### US_soft_gate_override_failed / US AMD / cand_f9e1434d9b53f47da29d

- 시각: 2026-07-07 2026-07-08T00:59:56+09:00
- Claude action: `BUY_READY`
- 기록 route: `WATCH` / `` / reason=`soft_gate_override_failed` / gate=`soft_gate_override_failed` / no_submit=`None`
- 분류: `data_or_wiring_gap_recovers`
  - 주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성
  - 회복 시나리오: clear_soft_blocks

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| complete_post_open_data | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| complete_post_open_no_orb | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| inside_entry_cap | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| complete_pathb_plan | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |
| clear_soft_blocks | confirmed/BUY_READY | BUY_READY/PlanA.buy | buy_ready |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | WATCH/None | soft_gate_override_failed |  |

### US_pathb_claude_price_invalid / US ORCL / cand_37abbe69b7694804e7e5

- 시각: 2026-07-01 2026-07-01T23:01:36+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `PULLBACK_WAIT` / `PathB.wait` / reason=`pullback_wait` / gate=`` / no_submit=`CLAUDE_PRICE_INVALID`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 CLAUDE_PRICE_INVALID 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_data | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| inside_entry_cap | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_pathb_plan | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |

### US_pathb_claude_price_invalid / US ORCL / cand_964f7e17da722d8186f8

- 시각: 2026-07-01 2026-07-01T23:01:36+09:00
- Claude action: `PULLBACK_WAIT`
- 기록 route: `PULLBACK_WAIT` / `PathB.wait` / reason=`pullback_wait` / gate=`` / no_submit=`CLAUDE_PRICE_INVALID`
- 분류: `submit_layer_block_after_route_open`
  - route는 기록상 이미 열렸지만 submit 단계에서 CLAUDE_PRICE_INVALID 발생
  - evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함

| scenario | evidence | route | gate reason | missing |
|---|---|---|---|---|
| original | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_data | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_post_open_no_orb | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| inside_entry_cap | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| complete_pathb_plan | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| clear_soft_blocks | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |
| kr_confirmation_confirmed | confirmed/BUY_READY | PULLBACK_WAIT/PathB.wait | pullback_wait |  |

