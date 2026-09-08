# Pipeline Simulation Matrix

- since: `2026-07-01`
- market: `both`
- rows: 90,100
- replayable evidence rows: 84,611
- replayable route rows: 90,100

## Fidelity

- evidence: `{'state_match': 32124, 'ceiling_match': 32124, 'state_mismatch': 56, 'ceiling_mismatch': 56}`
- route: `{'action_match': 31785, 'route_match': 31730, 'action_mismatch': 395, 'route_mismatch': 450, 'unreplayable': 1}`
- route mismatch top: `{'HARD_BLOCK->WATCH': 232, 'PULLBACK_WAIT->WATCH': 136, 'WATCH->PROBE_READY': 12, 'WATCH->BUY_READY': 8, 'HARD_BLOCK->BUY_READY': 5, 'BUY_READY->WATCH': 2}`

## US

| stage | count | survival | sessions |
| --- | ---: | ---: | ---: |
| candidate | 66,550 | 100.00% | 15 |
| prompt | 25,881 | 38.89% | 15 |
| actionable | 599 | 2.31% | 5 |
| evidence_pass | 591 | 98.66% | 5 |
| route_pass | 494 | 83.59% | 5 |
| entry_wiring | 494 | 100.00% | 5 |
| safety_submit | 427 | 86.44% | 5 |
| candidate_audit_fill | 0 | 0.00% | 0 |
| canonical_fill_fallback | 12 | n/a | 2 |
| canonical_closed_fallback | 12 | 100.00% | 2 |

### Top blocks

- prompt: `{'hard_cap_cutoff': 37141, 'no_reason': 1838, 'data_insufficient(0usable)': 1348, 'data_insufficient_cooldown(43usable)': 37, 'data_insufficient_cooldown(55usable)': 32, 'data_insufficient_cooldown(54usable)': 31, 'data_insufficient(23usable)': 30, 'data_insufficient_cooldown(47usable)': 25, 'data_insufficient_cooldown(57usable)': 24, 'data_insufficient_cooldown(52usable)': 23}`
- actionable: `{'WATCH': 21322, 'no_action': 3696, 'AVOID': 264}`
- evidence_pass: `{'ceiling=WATCH/confirmed': 8}`
- route_pass: `{'WATCH:soft_block_floor:late_mover': 27, 'WATCH:inside_buy_zone': 12, 'WATCH:soft_block_floor:repeated_failed_ready': 12, 'WATCH:pullback_wait_evidence_gate': 12, 'HARD_BLOCK:-': 8, 'WATCH:soft_gate_override_failed': 8, 'WATCH:-': 7, 'WATCH:negative_pullback_context': 5, 'WATCH:require_trade_ready': 3, 'WATCH:above_pathb_buy_zone': 3}`
- safety_submit: `{'NO_SIGNAL': 67}`
- candidate_audit_fill: `{'candidate_audit_filled_count_zero': 427}`
- canonical_fill_fallback: `{'no_canonical_fill_for_candidate_key': 415}`

### Canonical fill truth

- canonical_filled_unique: 4
- canonical_closed_unique: 3
- canonical_filled_seen_in_safety_candidates: 2
- canonical_closed_seen_in_safety_candidates: 2
- canonical_filled_decision_ids: 4
- canonical_decision_seen_any_candidate_row: 2
- canonical_decision_seen_executable_route_row: 1
- canonical_decision_seen_after_safety_stage: 1
- candidate_audit_fill_rows: 0
- candidate_audit_execution_decision_rows: 462
- candidate_audit_execution_event_rows: 0

### Missing-field counterfactual

- downgraded_rows: 7,528
- missing_fields_top: `{'opening_range_break': 3460, 'vwap_distance_pct': 3251, 'volume_ratio_open': 3211, 'ret_3m_pct': 1894, 'ret_5m_pct': 1894, 'current_price': 1}`
- recovered_to_buy_ready: `{'fill_volume_ratio_open': 24, 'fill_opening_range_break': 1313, 'fill_vwap_distance_pct': 0, 'fill_three_confirmation_fields': 1337}`
- time_normalized_rvol: present=3,704, recovered=19

### Route counterfactual

- buy_ready_rows: 386
- route_distribution: `{'PlanA.buy': 347, 'missing': 35, 'PlanA.probe': 4}`
- force BUY_READY -> PlanA.buy flips: 13/39

## KR

| stage | count | survival | sessions |
| --- | ---: | ---: | ---: |
| candidate | 23,550 | 100.00% | 15 |
| prompt | 13,421 | 56.99% | 15 |
| actionable | 222 | 1.65% | 10 |
| evidence_pass | 214 | 96.40% | 10 |
| route_pass | 39 | 18.22% | 7 |
| entry_wiring | 39 | 100.00% | 7 |
| safety_submit | 39 | 100.00% | 7 |
| candidate_audit_fill | 0 | 0.00% | 0 |
| canonical_fill_fallback | 1 | n/a | 1 |
| canonical_closed_fallback | 1 | 100.00% | 1 |

### Top blocks

- prompt: `{'hard_cap_cutoff': 8087, 'no_reason': 1203, 'data_insufficient(0usable)': 764, 'data_insufficient(33usable)': 27, 'data_insufficient(34usable)': 20, 'data_insufficient(35usable)': 16, 'data_insufficient(36usable)': 3, 'data_insufficient(25usable)': 2, 'data_insufficient(27usable)': 2, 'data_insufficient(30usable)': 2}`
- actionable: `{'WATCH': 9347, 'no_action': 3507, 'AVOID': 345}`
- evidence_pass: `{'ceiling=WATCH/confirmed': 8}`
- route_pass: `{'WATCH:-': 124, 'WATCH:entry_price_cap_exceeded': 13, 'WATCH:inside_buy_zone': 7, 'WATCH:pullback_wait_evidence_gate': 7, 'WATCH:kr_fast_trigger_not_confirmed': 6, 'WATCH:soft_block_floor:late_mover': 5, 'WATCH:negative_pullback_context': 5, 'WATCH:kr_early_entry_buy_demoted_to_probe': 4, 'WATCH:kr_late_fresh_buy_demoted_to_probe': 2, 'WATCH:soft_block_floor:repeated_failed_ready': 2}`
- candidate_audit_fill: `{'candidate_audit_filled_count_zero': 39}`
- canonical_fill_fallback: `{'no_canonical_fill_for_candidate_key': 38}`

### Canonical fill truth

- canonical_filled_unique: 3
- canonical_closed_unique: 1
- canonical_filled_seen_in_safety_candidates: 1
- canonical_closed_seen_in_safety_candidates: 1
- canonical_filled_decision_ids: 3
- canonical_decision_seen_any_candidate_row: 1
- canonical_decision_seen_executable_route_row: 1
- canonical_decision_seen_after_safety_stage: 1
- candidate_audit_fill_rows: 0
- candidate_audit_execution_decision_rows: 38
- candidate_audit_execution_event_rows: 0

### Missing-field counterfactual

- downgraded_rows: 7,118
- missing_fields_top: `{'opening_range_break': 2443, 'volume_ratio_open': 2218, 'vwap_distance_pct': 2217, 'ret_3m_pct': 1697, 'ret_5m_pct': 1697, 'current_price': 591}`
- recovered_to_buy_ready: `{'fill_volume_ratio_open': 8, 'fill_opening_range_break': 502, 'fill_vwap_distance_pct': 0, 'fill_three_confirmation_fields': 522}`
- time_normalized_rvol: present=3,547, recovered=3

### Route counterfactual

- buy_ready_rows: 92
- route_distribution: `{'missing': 82, 'PlanA.buy': 10}`
- force BUY_READY -> PlanA.buy flips: 8/82
