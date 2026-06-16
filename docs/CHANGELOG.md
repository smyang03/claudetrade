# 운영 변경 일지 (CHANGELOG)

운영자가 "언제 무엇을 왜 바꿨고 지금 어떤 상태인지"를 한 파일로 따라가기 위한 일지.
git(코드 diff) + 이 파일(결정·토글 이력) + `/status`(현재 스냅샷) = 통제권 3종 세트.

기록 규칙:
- 코드/config/env/토글을 바꾸면 **무엇 / 왜 / 현재상태 / 롤백조건 / 커밋해시**를 한 줄로 추가.
- **롤백·번복도 기록한다** (조용히 엎지 않기). 기각한 것도 "(기각) 이유"로 남겨 반복 논의 방지.
- 최신 날짜가 위로. 상세 근거는 CLAUDE.md `MD 위반 사항`/메모리에, 여기는 한눈에 보는 요약.

---

## 2026-06-16

### 운영 통제 / 원칙
- **현실적 직언 원칙** 추가 — 긍정 편향 말고 데이터대로. [CLAUDE.md]
- **운영자 파악 가능성 원칙** 추가 — 변경 시 무엇/왜/현재상태 매번 명시, 롤백도 보고. [`4c662fc`]
- **운영 변경 일지(이 파일)** 신설.
- 커맨드 신설(.claude/commands, 로컬·gitignore): `/status`(현재 토글+커밋+봇상태), `/check`(검증), `/capture`(성과), `/monitor`(라이브점검), `/saveyou`(세션저장).

### 트레이딩 (재배선 1단계 — 청산 변별)
- **weak_mfe_cut OFF** (US/KR) — 단타 손절은 번지수 오인(손실 HOLD=stop_recovery는 정상). 청산을 hold advisor에 환원. **롤백**: `(US_/KR_)PATHB_WEAK_MFE_CUT_ENABLED=true`. 코드 보존. [`bcbd8ff`]
- **hold advisor 이익보호 prior ON** (`HOLD_ADVISOR_PROFIT_GUARD_ENABLED=true`) — 이익 중 HOLD 반납(70%, profit_pullback -8.91%p) 방지·익절 우선, 단 추세 살아있으면 러너 HOLD 유지. A/B 변별 -10%p→+25%p. **롤백**: 토글 false. **라이브 미검증 → 미국장 모니터.** [`bcbd8ff`]
- **capture_net_review 확장** — 실측 MFE 기반 net capture + 월별 국면 분해(forward 착시 분리). [`bcbd8ff`]
- 분석 도구 신설: hold_advisor_quality_test / ab_test / discriminate_test. [`bcbd8ff`]

### 진입 (momentum 진단)
- **momentum early-entry 진단 임계를 게이트와 통일** — momentum_wait 오분류 제거. [`cbb727b`]
- KR 청산 tp_check 미설정 KeyError 가드. [`67a6549`]

### 기각 (왜 안 했나 — 반복 논의 방지)
- **잡전략 OFF** — 무효. 손실의 PathB 경로(claude_price)라 전략 토글이 안 먹음.
- **profit_ladder 강화** — 보호영역 + 2026-06-14 롤백 이력. 설계+백테스트 선행 필요.
- **진입 빈도↓ (confidence↑)** — 진입 단일경로라 수익(target)도 위축.
- **진입 타이밍 튜닝** — 못 이기는 게임 + 반복 실패. 눌림매수 추종형은 구조적으로 늦음(정상).

### 검증 대기 (다음 행동)
- 미국장 라이브: prior가 giveback 줄이나 + **좋은 러너 오절단 부작용 없나** + weak_mfe OFF가 회복 vs 손실확대. 악화 시 prior 토글 롤백.
- 다음 숙제: 진입 변별 shadow → 측정 재정의 코드 → 수수료/KR.
