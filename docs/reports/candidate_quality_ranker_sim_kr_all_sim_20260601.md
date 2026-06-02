# Candidate Quality Ranker Simulation

- generated_at: 2026-06-01T14:53:23
- scope: local DB only; no broker/API/Claude calls
- label_policy: forward/outcome labels are used only after scoring for evaluation

## Basis

- rows: 41357
- date_min: 2026-04-20
- date_max: 2026-06-01
- caps: [25, 30, 36]

## Scenario Metrics

| scenario | n | ret60_n | ret60_avg | ret30_avg | good_rate | bad_rate | pf60 |
|---|---:|---:|---:|---:|---:|---:|---:|
| current_prompt_cap25 | 17734 | 2975 | -0.1158 | 0.1297 | 27.46 | 37.6 | 0.9206 |
| trainer_prompt_cap25 | 19735 | 3036 | -0.0226 | 0.1896 | 27.96 | 37.36 | 0.9834 |
| trainer_plan_a_shadow_cap25 | 6145 | 1151 | 0.3834 | 0.566 | 32.15 | 38.26 | 1.2261 |
| current_prompt_cap30 | 19691 | 3400 | 0.0019 | 0.201 | 28.21 | 36.79 | 1.0014 |
| trainer_prompt_cap30 | 22817 | 3582 | 0.0774 | 0.2457 | 28.75 | 36.83 | 1.0594 |
| trainer_plan_a_shadow_cap30 | 6333 | 1202 | 0.5286 | 0.6917 | 33.03 | 37.69 | 1.3088 |
| current_prompt_cap36 | 20560 | 3655 | 0.0674 | 0.2257 | 29.19 | 36.08 | 1.0506 |
| trainer_prompt_cap36 | 25912 | 4167 | 0.1027 | 0.2129 | 29.42 | 36.19 | 1.08 |
| trainer_plan_a_shadow_cap36 | 6400 | 1218 | 0.5064 | 0.6851 | 32.76 | 37.7 | 1.2901 |

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
