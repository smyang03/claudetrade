# 자율 디버깅 — 데이터 흐름 무결성 (2026-07-23 밤, US 세션 중)

운영자: 재시작 후 오늘 수정분 forward 반영 + 데이터 흐름 데이터 기반 확인·모니터링.

## 1. ★ 발견·수정: trainer_tier/cohort_reliability 컬럼 전멸

**증상**: audit_candidate_rows.trainer_tier 0건인데 소스 runtime_gate.trainer_tier는 60,851행 존재.
cohort_reliability도 60/60,851뿐(사실상 전멸).

**근본 원인**: 추가컬럼 해석(_candidate_extra_value)이 raw row["payload"]를 읽는데, runtime_gate는
route/execution 단계(`execution_context["trainer_tier"]`, trading_bot 8975)에서 완성되어 병합
payload로만 존재. upsert_candidate의 selection-단계 write와 어긋난다.

**수정**:
- forward(부분): upsert_candidate 해석을 effective_payload 기준으로(커밋 9988a32). **단 이건
  selection-단계만 커버**. 실측 결과 execution-단계로 완성되는 US 라이브 행은 여전히 0
  (백필 후 창 US 0/131). = **forward 미완, execution-write 경로 해석 추가가 남은 근본 fix.**
- backfill(결정적): tools/backfill_trainer_tier_from_payload.py — 저장 payload에서 복구.
  60,851행 복구, US 7/23 → 524/568(나머지 44는 소스 없음=정상).

**영향도 = 낮음(audit-only)**: trainer_tier는 라이브 판정 시 **후보 객체에서 직접** 읽힌다
(action_routing:518 KR WATCH gate, candidate_quality_trainer, analysts). audit 컬럼은 **사후
분석 전용** — 비어도 주문·리스크·net 무영향. 분석 전 백필하면 정합.

**체계 스윕**: 154개 EXTRA_CANDIDATE_COLUMNS 전수 대조(payload 해석가능 vs 컬럼 기입).
**이 부류 결함은 trainer_tier 하나뿐** — 형제 없음, 부류 봉쇄.

**남은 forward fix(후속, 라이브 세션 중 미실행)**: update_execution_by_candidate_key/
_by_ticker가 payload를 저장할 때 _RUNTIME_GATE_SOURCED 컬럼도 해석하도록. 런타임 순서
검증 필요 — 다음 세션 착수. 그때까지 주기 백필로 커버.

## 2. rel_vol_shadow — candidate_audit 직렬화 갭 (관측용, 저우선)

- **산출·persist 정상**: ticker_selection_log.rel_vol_shadow 1,102건, baseline 정상(NVDA 130M 등).
- **갭**: candidate_audit 0건. selection_meta upsert dict(trading_bot 20544)에 rel_vol_shadow
  필드가 **아예 없음**(명시 키만 담고 prompt_row 통째로 안 실음).
- **판정**: 관측 전용 필드이고 데이터는 selection_log에 있어 분석 가능. 저우선. fix=그 dict에
  3필드 추가(prompt_row가 담고 있으면). 라이브 중 미실행.

## 3. 검증 완료(정상) 흐름

- ✅ mfe_time(150) / canonical.mfe_pct 전파(317=learning 정합)
- ✅ 체결귀속 110,185건 매칭 흐름
- ✅ non_pathb excursion: SCHG(US) observed 7·mfe −3.25% (어젯밤 "US 세션 대기" 판정 적중)
- ✅ recheck 정렬 shadow 배선(funnel)
- ✅ early_path tighten shadow 배선(risk_manager:746), 적색 마크 건만 발동(정상 가드)

## 4. US 세션 게이팅 검증(정상)

- MARKET_CLOSED 로그 = 22:29(개장 22:30 **전** 잔여). ENTRY_BLACKOUT = 22:34(장초반 0~30분 게이트).
  22:37 market_open 트리거로 US 정상 인식. = 버그 아님.
- **BUY_READY 발동 0 = 안전장치 정상**: US 국면이 MILD_BEAR/CAUTIOUS_BEAR(약세)라 regime_blocked.
  B 시뮬의 "MILD_BEAR 최악" 회피 실시간 실증("regime is the edge"). 강세/중립 국면에서만 발동.
- 부수 발견: **CAUTIOUS_BEAR** 국면 존재(중립 CAUTIOUS와 별개 약세 계열) → 허용목록 밖, 차단 타당.

## 5. AI 규율 회고 (이 세션 내 오류)

- 소표본 함정 반복: runtime_gate "없음"(5행)·cohort "됨"(60건)·forward "작동"(백필을 오인) —
  **세 번** 성급 단정, 매번 전수/시점 실측으로 교정. "평균의 오류·시점 단정 금지" 재체득.
- "0건≠결함"은 정확히 적용(excursion/early-path를 장 대기로 분류 → 적중).
- 교훈: 라이브 write 경로의 forward 여부는 **백필 창 이후 행**으로만 판정(백필 결과를 forward로 오인 금지).
