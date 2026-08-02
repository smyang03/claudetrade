# 개편 후 시스템 운영 구조 — 전면 분석·검증 보고 (2026-08-02)

운영자 질문 전체에 대한 실측 기반 답. 검증 방법은 각 절에 명시. 관련: restructure_full_report_20260802.md, design_kr_fallen_lane_spec_20260802.md.

---

## 1. 지금 시스템이 무엇에 집중하는가 (한 문장)

**"저평가(과잉반응 급락) 종목을 코드로 골라 소액 매수하고, TP12/SL25/D5 규칙으로 상승을 기다린다. Claude는 선정하지 않고 매도 리뷰와 관측 데이터 생산만 한다."**

| 축 | US | KR |
|---|---|---|
| 저평가 정의 | 장중 투매형 급락 (하드필터 5조건) | 갭 과잉반응형 급락 (8조건) |
| 선정 주체 | **코드** (GBM 알파모델 + day_losers 소스 필터) | **코드** (8조건 AND — 모델은 리프트 확보 전 미사용) |
| 탐색 시점 | 장중 (개장 5~30분 진입창) | **장 마감 후** (close+40분 자동 스캔, 익일 시가 진입 규약) |
| 실주문 | micro 3슬롯/일1건/30만원 (08-02 운영자 결정) | **없음 — shadow만** (4~6주 forward 게이트 후 micro 논의) |
| 매도 | TP+12%/SL−25%/D5 자동 + Claude 매도리뷰 | (shadow 정산 동일 규약) |

Claude 실시간 후보 판단은 **필요 없어졌고 실제로 주문에 관여하지 않는다** — selection/judge는 LEGACY 스위치에 의해 주문 경로에서 차단된 채 shadow 데이터만 생산한다(모델도 haiku로 하향, 일 ~$2).

## 2. 매수 경로 실측 (08-02 재기동 검증)

- 유일한 실주문 경로: `runtime/us_swing_order_bridge.py` (micro probe). 게이트 통과 순서: authority(override 슬롯3) → 브로커 truth fail-closed → 공통 매수 게이트(RISK_HALTED 신설 포함) → 예산/현금 → 제출.
- 차단 3중 방벽: `LEGACY_NEW_BUY_DISABLED=true` + `PATHB_*_LIVE_ENABLED=false` + `PROFIT_STRATEGY_ENABLED_IDS=` 빔.
- **개편 누락 1건 발견·수정**: 가디언 smoke가 LEGACY 차단을 hard_fail로 분류해 `BLOCK_START` → 봇 기동 반복 실패(08-02 실측). smoke/preflight가 "의도된 차단=정상"으로 판정하게 수정(b08e292). preflight 빈 arm set 계약도 동일 계열 수정(5db9b0e).

## 3. PathA/PathB — 폐기가 아니라 "플래그 오프 보존"

- **코드 무손상.** 이번 개편은 config 스위치만 바꿨다(678604c 실측: 스위치·캘리브레이션 값만 변경).
- 복원 절차: `.env.live`+`v2_start_config.json` 두 소스에서 `LEGACY_NEW_BUY_DISABLED=false`, `PATHB_KR/US_LIVE_ENABLED=true`, (코어까지면 `PROFIT_STRATEGY_ENABLED_IDS` 원복) → 재시작 → effective-config 실측. 롤백 맵은 monday_readiness_20260801.md §1-1.
- ⚠️ 단 **PathB 플랜 "데이터"도 현재 안 쌓인다** (live+`PATHB_*_SHADOW_PLAN_ENABLED` 모두 false → 플랜 등록 스킵 실측). 플랜 원장만 계속 쌓고 싶으면 `PATHB_*_SHADOW_PLAN_ENABLED=true`가 별도 옵션 — 운영자 결정 항목.

## 4. DB·데이터 파이프라인 연속성 (전부 실측)

| 파이프라인 | 상태 | 근거 |
|---|---|---|
| Claude selection/judge/analyst → selection·decisions DB | **지속** | LEGACY 차단은 주문 시점(safety_gate)에만 걸림. 케이던스·스키마 유지, 모델만 haiku(3349956) |
| 후보풀·스크리너·rescreen(30분)·sub_screener(10분) | 지속 | start-config 스위치 전부 on 실측 |
| candidate_audit / ticker_selection_log / decisions.db | 지속 | max_rowid·mtime 실측(감사 MCP get_recent_db_health) |
| us_swing shadow (신호·하드필터 관측) | 지속 | `US_SWING_SHADOW_SCHEDULER_ENABLED=true`, 세션마다 자동 |
| KR 급락 shadow | **신설·자동화** | close+40분 스케줄러 잡(298aa62), 중복방지 실데이터 검증 |
| 코어 sleeve 추적·매니페스트 | 지속 | core_shadow_tracker 6h 루프, NO_LIVE_AUTHORITY 매니페스트 신선 |
| PathB 플랜 원장 | **중단됨(의도)** | §3 참고 — 재개 옵션 존재 |
| PEAD/어닝 캘린더 수집 | 지속 | 입력 품질 기능, 개편 무관 |

"나중에 DB로 전략을 만들 수 있는가" — 예. 수집 계약(가격·시점 정본, forward 라벨, shadow 원장)이 유지되므로 이번 개편 기간의 데이터도 소급 분석 가능하다.

## 5. 데이터 주입 디버깅 (오늘 수행한 것)

1. 브리지 e2e: 가짜 신호를 DB에 주입해 제출/차단/중복/슬롯3/스냅샷 stale·예외 fail-closed/강제갱신 회복 10케이스 실행.
2. KR 스캐너: 실캐시(641종목)로 07-31 스캔 재실행 — 중복 2건 스킵·0건 기록, 정산은 익영업일 전 0건(규약대로).
3. 스케줄러: 시각 픽스처로 KR 잡 발화(16:10)/조기 미발화/플래그 off 검증.
4. smoke: LEGACY on/off 양방향 실행 — on이면 legacy_block_expected, off면 기존 경로 통과.
5. 전체 회귀 3387 테스트 + preflight 161체크 FAIL 0 + 월경계 재시작 로직 시나리오 5종.

## 6. 운영자 질문별 답

- **장판단(30분 rescreen) 유지?** — 유지 권고. 소비처가 남아 있다: consensus mode가 micro probe 주문 mode 라벨·HALT/국면 문맥에 쓰이고, 08-02 운영자 지시가 "데이터 축적 유지, 비용만 축소"였다(이미 haiku 하향으로 일 ~$2). 더 줄이려면 `RESCREEN_INTERVAL_MIN` 30→60이 레버지만 라벨 케이던스가 바뀌므로 운영자 결정.
- **매도에 API 사용?** — 현행 유지: TP/SL/D5는 규칙 자동, `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`로 Claude 리뷰(sonnet 유지). 변경 불요.
- **보유 금액 소진 시 후보는?** — 주문만 차단되고 관측은 계속된다. 브리지가 현금·예산을 매 신호 평가에 넣어 `order_budget_unavailable`/`micro_budget_cannot_buy_one_share`로 기록하고, 신호 자체는 shadow DB·handoff 원장에 남는다. 이월 큐는 없다(익일 새 신호로 재평가) — 급락 반등은 시효가 있어 이월하지 않는 것이 설계상 옳다.
- **장이 없을 때 저평가 탐색?** — KR은 이미 장외(마감 후) 스캔 구조다. 주말·장외 심화(재무·공시 기반 저평가 검증)는 MCP 3순위 DART·KRX 축이며, KR 스펙 문서의 valuation 피처 추가 계획과 연결된다. 실주문 경로에는 연결하지 않는다.

## 7. MCP — 반영 현황과 로드맵 판정

운영자 계획(5순위 체계)은 방향이 맞다. 원칙 재확인: **MCP는 검증·감사·리서치 보조이며 주문 루프와 완전 분리, KIS가 시세·주문·보유의 유일 기준.**

- **1순위 자체 감사 MCP — 구축 완료**(c36085c): `tools/claudetrade_audit_mcp.py`, stdlib 전용(서드파티 0), 읽기 전용 7도구(effective-config redacted·매수게이트 요약·us_swing 권한·보유/미체결·shadow 성과·DB 건강). 모든 응답에 as_of/source/data_age_sec/schema_version. `.mcp.json` 등록, 스모크 통과. 쓰기 도구 없음.
- 2순위 SQLite 조회 MCP — 감사 MCP의 DB 도구가 1차 커버. 범용 자연어 조회는 서드파티 npm 서버 검증 후(브로커 자격증명 있는 라이브 머신에 미검증 코드 설치는 금지 계열) — 장 시작 직전 도입 부적절, 주중 검토.
- 3순위 DART·KRX — KR valuation shadow 축과 연결. update-cache→shadow 구조 조건부 찬성. pykrx 타임아웃 교훈(무타임아웃 금지) 적용.
- 4순위 Alpha Vantage — 일 25회 한도 내 사후 교차검증만. 스크리너·실시간 판단 연결 금지 동의.
- 5순위 Inspector — 신규 서버 연결 시 사용.

## 8. 남은 리스크·운영자 확인 항목

1. **세션 외 기존 변경 미커밋**: `execution/single_symbol_judge.py`(judge 손절폭 env화 — Codex P2, 이 세션 작업 아님), CLAUDE.md, 테스트 2건. 커밋/폐기 결정 필요.
2. PathB 플랜 데이터 중단(§3) — 재개 여부.
3. day_losers 전용 전환 후 forward 표본 0 — 월요일부터 축적. 일일 확대(1→3/일)는 forward ≥30건 + 순성과 양수 후.
4. 월요일 감시 포인트: `LEGACY_BUY_DISABLED` 차단 로그 실측, us_swing handoff 로그, KR 스캔 잡 16:10 발화, guardian 하트비트.
5. 슬롯 카운트는 로컬 메타데이터(source_strategy) 의존 — 브로커 sync가 메타데이터를 잃으면 과소계상 가능(Codex P1). 슬롯 3이라 노출 한정적, 관측 후 필요시 보강.
