# 세션 핸드오프 — 2026-06-27 (재시작 후 확인 + 다음 세션)

스크리너·진입·capture 심층 분석 + ladder A/B(Phase B) enforce 구현. 라이브 실측은 봇 재시작 + 거래 누적 필요라 미완(시간 게이트). 메모리: ladder-ab-phase-b-20260627, screener-entry-verdict-20260627, analysis-script-runbook.

## 1. 오늘 라이브 적용/커밋 (브랜치 feat/net-hygiene-improvements-20260626)
- ladder A/B (Phase B) c22d1f8 — US_LADDER_AB_MODE=enforce. B그룹(decision_id 해시 50%)=peak-trail(MFE 4%+ 면 peak-2%), A=현행. KR off. 검증도구 e312c98.
- 이전 커밋: A2 강등 1.0, fast_fill zone-cap, late-entry 게이트, 측정배관, MFE백필.
- 독성종목 격리는 되돌림(97963f7) — band-aid라 기각.
- config: .env.live(gitignore) + v2_start_config.json 둘 다. 재클론시 .env.live 별도.

## 2. 재시작 후 확인 (봇 PID 11188은 구코드 — 재시작해야 발효)
- 발효: ladder A/B, 이미 켜진 zone-cap·late-gate·A2·A1.
- TODO #9: 로그에서 late_gate_action(오후), zone 위 체결 없음, A1 차단 확인. /botstatus + live_preflight.py.

## 3. ladder A/B 실측 (TODO #8 — 재시작 + 며칠 거래 후)
- python tools/ladder_ab_review.py --market US --since 2026-06-27
- 판정: delta(B-A) net_avg 가 +0.3%p(noise) 초과 AND 악화반납 통제면 유지. 음전이면 US_LADDER_AB_MODE=off.
- 주의: yfinance sweep delta 과대(악화 12건 실재), baseline 노이즈 ±0.2~0.3%p, 단일국면.

## 4. 다음 세션이 알 것 (오늘 결론)
- 스크리너 랭킹 건드리지 마라 — A-B-C에서 trainer 리랭킹 양 시장 backfire. execution이 레버.
- 눌림 진입 정당 — 추격 -EV, 놓친 runner는 giveback 환상.
- capture가 본체 — 거래 +2~3% 도달하는데 TARGET 외엔 토함. ladder A/B가 대응.
- 월별 발산: US 6월 -0.71(악화), KR 6월 +1.05(개선). 단일국면 주의, KR 축소 금지.
- DO#4(월요일 A1 재측정), passive 검토 대기.

## 5. 미결 결정 (운영자)
- ladder A/B에 shadow 모드 추가 vs 현 enforce 유지.
- PR 생성·main 머지 (운영테스트 후).
