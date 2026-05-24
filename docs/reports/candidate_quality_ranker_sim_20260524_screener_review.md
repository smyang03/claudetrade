# Candidate Quality Ranker Simulation

- generated_at: 2026-05-24T10:14:27
- scope: local DB only; no broker/API/Claude calls
- label_policy: forward/outcome labels are used only after scoring for evaluation

## Basis

- rows: 30485
- date_min: 2026-04-20
- date_max: 2026-05-22
- caps: [25, 30, 36]

## Scenario Metrics

| scenario | n | ret60_n | ret60_avg | ret30_avg | good_rate | bad_rate | pf60 |
|---|---:|---:|---:|---:|---:|---:|---:|
| current_prompt_cap25 | 13634 | 1332 | 0.2922 | 0.2492 | 27.18 | 31.83 | 1.302 |
| trainer_prompt_cap25 | 15620 | 1381 | 0.4241 | 0.3376 | 27.66 | 32.26 | 1.5114 |
| trainer_plan_a_shadow_cap25 | 3752 | 358 | 1.4742 | 0.9961 | 38.27 | 30.76 | 2.7859 |
| current_prompt_cap30 | 14771 | 1461 | 0.3517 | 0.2691 | 27.17 | 32.12 | 1.3735 |
| trainer_prompt_cap30 | 17882 | 1621 | 0.4044 | 0.301 | 27.88 | 32.52 | 1.4733 |
| trainer_plan_a_shadow_cap30 | 3884 | 379 | 1.5343 | 1.0109 | 39.31 | 30.42 | 2.6926 |
| current_prompt_cap36 | 15111 | 1508 | 0.3831 | 0.2749 | 27.72 | 31.77 | 1.4174 |
| trainer_prompt_cap36 | 19993 | 1834 | 0.4142 | 0.2638 | 28.68 | 32.38 | 1.4725 |
| trainer_plan_a_shadow_cap36 | 3924 | 386 | 1.5147 | 1.0169 | 39.64 | 30.31 | 2.6099 |

## Promoted Examples

| market | ticker | score | state | ret60 |
|---|---|---:|---|---:|
| KR | 010170 | 65.0 | PLAN_B | None |
| KR | 011930 | 65.0 | PLAN_B | None |
| KR | 032820 | 65.0 | PLAN_B | None |
| KR | 042510 | 65.0 | PLAN_B | None |
| KR | 049080 | 65.0 | PLAN_B | None |
| KR | 065770 | 65.0 | PLAN_B | None |
| KR | 069540 | 65.0 | PLAN_B | None |
| KR | 084650 | 65.0 | PLAN_B | None |
| KR | 085670 | 65.0 | PLAN_B | None |
| KR | 093370 | 65.0 | PLAN_B | None |

## Omitted Examples

| market | ticker | old_rank | ret60 |
|---|---|---:|---:|
| KR | 006345 | 1 | None |
| KR | 001780 | 5 | None |
| KR | 026940 | 6 | None |
| KR | 001510 | 9 | None |
| KR | 005010 | 10 | None |
| KR | 047040 | 12 | None |
| KR | 009830 | 13 | None |
| KR | 005930 | 15 | None |
| KR | 006340 | 16 | None |
| KR | 058430 | 18 | None |
