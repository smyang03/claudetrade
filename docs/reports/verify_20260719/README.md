# 검증 스크립트 번들 (2026-07-19)

alpha_hunt_and_design_20260719.md·six-visions·anti-chase enforce 결정의 재현 스크립트.
전부 read-only(DB) 또는 외부 yfinance. 결과 요약은 상위 리포트·메모리 참조.

- loss_profit_decomp.py / alpha_hunt.py — 손익 구조 분해(볼록 출구 vs churn, gross vs net, 보유기간)
- max_lottery_test.py / anti_chase_counterfactual.py — anti-chase 우리 net 검증(MAX≥20% 독성)
- external_validation.py / external_validation2.py — anti-chase·저변동·turn-of-month 외부 검증
- verify_visions.py / verify_visions_ext.py — 여섯 비전 검증(볼록성·경로유전자 r0.49·캐릭터·너비·안티프래질)
- sim_confirmation_exit.py — ride-규칙 외부 시뮬(컷 반증, 연장 실익)
- backfill_path_genome_daily.py — ★ride-규칙 우리 book 백필(외부와 반대로 refute)
